"""Windows-first local MRI -> FreeSurfer -> DunedinPACNI pipeline.

All images and reconstructed outputs stay on local disk. Docker Desktop
(Linux containers), Python 3.9+, and a private FreeSurfer license are required.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import math
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


def upscaler():
    """Load optional SynthSR module without changing existing CLI imports."""
    path = ROOT / "scripts" / "synthsr_upscale.py"
    spec = importlib.util.spec_from_file_location("synthsr_upscale", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def synth_index(study):
    return upscaler().read_index(study)


def active_input(study, variant="original"):
    if variant == "original":
        return select_input(study)
    if variant != "synthsr":
        raise PipelineError("Unknown source variant: " + variant)
    item = synth_index(study)
    if not item:
        raise PipelineError("No verified SynthSR derivative. Run Upscale your MRI first.")
    return item["output_path"]



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


def subject_id(study, variant="original"):
    if variant == "synthsr":
        item = synth_index(study)
        if not item:
            raise PipelineError("SynthSR derivative missing or stale; rerun upscaling")
        return subject_id(study) + "_synthsr_" + item["source_sha256"][:8]
    if variant != "original":
        raise PipelineError("Unknown input variant")
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


def subject_dir(study, variant="original"):
    return SUBJECTS / subject_id(study, variant)


def provenance_path(study, variant="original"):
    """Store pipeline input provenance OUTSIDE FreeSurfer-managed subject dirs."""
    return PROVENANCE / (subject_id(study, variant) + ".json")


def provenance(study, image, variant="original"):
    folder = subject_dir(study, variant)
    if folder.is_symlink():
        raise PipelineError("Refusing to reuse a symlinked FreeSurfer subject directory")
    if variant == "synthsr":
        index = synth_index(study)
        if not index or image.resolve() != index["output_path"].resolve():
            raise PipelineError("SynthSR output or source link changed")
        digest = hashlib.sha256(json.dumps({
            "source_sha256": index["source_sha256"],
            "output_sha256": index["output_sha256"],
            "model_ref": index["model_ref"],
        }, sort_keys=True).encode("utf-8")).hexdigest()
    else:
        digest = signature(study, image)
    wanted = {"signature": digest, "fs_image": FS_IMAGE, "variant": variant}
    # Old launcher wrote provenance inside the subject before calling -i,
    # which made FreeSurfer reject every initial import. Still read legacy
    # records to allow safe and backwards-compatible recovery.
    external = provenance_path(study, variant)
    legacy = folder / "pacni-input.json"
    found_any = False
    for record in (external, legacy):
        if record.is_symlink():
            raise PipelineError("Refusing to use symlinked provenance file")
        if record.exists():
            found_any = True
            found = json.loads(record.read_text(encoding="utf-8"))
            if "variant" not in found and variant == "original":
                # Old provenance records predate source variants.
                found["variant"] = "original"
            if any(found.get(k) != v for k, v in wanted.items()):
                raise PipelineError(
                    "MRI input or FreeSurfer version changed: use a fresh scan folder")
    if folder.exists() and not found_any:
        raise PipelineError(
            "FreeSurfer subject exists without input provenance; "
            "inspect before reusing or resetting it")
    return wanted


def record_provenance(study, image, wanted, variant="original"):
    """Persist provenance before processing, without creating subject dir."""
    path = provenance_path(study, variant)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    relative = (image.relative_to(study) if variant == "original"
                else image.relative_to(ROOT))
    payload = dict(wanted, study=study.name, source=relative.as_posix())
    # Atomic replace prevents a partial JSON record after interruption.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def recover_launcher_stub(study, variant="original"):
    """Clean ONLY the exact one-file stub created by the old broken launcher.

    Do not delete any actual FreeSurfer output, including partial processing,
    and never delete an entire uninspected subject directory.
    """
    folder = subject_dir(study, variant)
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


def completed(study, variant="original"):
    return (subject_dir(study, variant) / "scripts" / "recon-all.done").is_file()


def reconstruct(study, variant="original"):
    image = active_input(study, variant)
    check_license()
    check_docker()
    meta = provenance(study, image, variant)
    if completed(study, variant):
        print("FreeSurfer already completed; reusing verified output.")
        return

    # Crucial invariant from the official FreeSurfer recon-all documentation:
    # -i must be provided only when the subject directory DOES NOT EXIST.
    # During resume, the already-imported 001.mgz is reused without -i.
    record_provenance(study, image, meta, variant)
    SUBJECTS.mkdir(parents=True, exist_ok=True)
    recover_launcher_stub(study, variant)
    folder = subject_dir(study, variant)
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
    # For SynthSR the generated T1 lives in work/upscaled, not MRI/<study>.
    # Both versions are the SAME acquisition; only their analysis workspaces differ.
    input_folder = study if variant == "original" else image.parent
    args = [
        "docker", "run", "--rm",
        "--mount", "type=bind,source=" + str(input_folder.resolve()) + ",target=/input,readonly",
        "--mount", "type=bind,source=" + str(SUBJECTS.resolve()) + ",target=/subjects",
        "--mount", "type=bind,source=" + str(LICENSE.resolve()) +
        ",target=/opt/pacni-license.txt,readonly",
        "-e", "SUBJECTS_DIR=/subjects", "-e", "FS_LICENSE=/opt/pacni-license.txt",
        FS_IMAGE, "recon-all", "-sd", "/subjects", "-s", subject_id(study, variant)
    ]
    if not already_imported:
        args.extend(["-i", "/input/" + image.relative_to(input_folder).as_posix()])
    args.extend(["-all", "-parallel", "-openmp", str(min(os.cpu_count() or 2, 8))])
    sh(args)
    if not completed(study, variant):
        raise PipelineError("FreeSurfer did not create recon-all.done; inspect recon-all.log")

def inspect(study, variant="original"):
    folder = subject_dir(study, variant)
    result = {"study": study.name, "subject_id": subject_id(study, variant),
              "source_variant": variant,
              "recon_complete": completed(study, variant),
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


def quality_gate(study, variant="original"):
    report = inspect(study, variant)
    out = RESULTS / subject_id(study, variant)
    out.mkdir(parents=True, exist_ok=True)
    (out / "qc.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["structural_qc"] != "pass":
        raise PipelineError("Incomplete or invalid FreeSurfer stats; scoring blocked")
    return report


def build_study_report(study, variant="original"):
    """Build an evidence-based research summary without starting Docker or altering MRI."""
    out = RESULTS / subject_id(study, variant)
    folder = subject_dir(study, variant)
    lines = [
        "DunedinPACNI — отчет по исследованию",
        "Исследование: " + study.name,
        "ID реконструкции: " + subject_id(study, variant),
        "Тип входа: " + ("СИНТЕТИЧЕСКИЙ SynthSR (НЕ оригинальная МРТ)"
                          if variant == "synthsr" else "оригинальная анатомическая МРТ"),
        "",
    ]

    image = None
    provenance_ok = False
    try:
        image = active_input(study, variant)
        display = image.relative_to(study) if variant == "original" else image.relative_to(ROOT)
        lines.append("T1: " + str(display))
        try:
            provenance(study, image, variant)
            provenance_ok = True
        except (PipelineError, OSError, ValueError) as error:
            lines.append("Целостность входных данных: НЕ ПОДТВЕРЖДЕНА (" +
                         str(error) + ")")
    except (PipelineError, OSError) as error:
        lines.append("T1: не найден или не выбран (" + str(error) + ")")

    finished = completed(study, variant)
    if finished:
        lines.append("FreeSurfer recon-all: завершен (recon-all.done)")
    elif folder.is_dir():
        lines.append("FreeSurfer recon-all: не завершен; обнаружен рабочий каталог")
    else:
        lines.append("FreeSurfer recon-all: не запускался / нет результатов")
    structurally_valid = False
    try:
        evidence = inspect(study, variant)
        structurally_valid = evidence["structural_qc"] == "pass"
    except (OSError, ValueError) as error:
        lines.append("Автоматический структурный QC: ошибка чтения (" + str(error) + ")")
    if structurally_valid:
        lines.append("Автоматический структурный QC: пройден (проверены файлы статистики)")
    elif finished:
        lines.append("Автоматический структурный QC: не пройден; требуется пункт 4")
    else:
        lines.append("Автоматический структурный QC: пока недоступен")
    lines.append("Визуальная оценка сегментации: не выполнялась автоматически")

    metadata_path = study / "scan.json"
    if metadata_path.is_file():
        try:
            acquisition = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(acquisition, dict):
                seq = acquisition.get("sequence")
                field = acquisition.get("field_strength_t")
                if seq:
                    lines.append("Протокол: " + str(seq))
                if field:
                    lines.append("Поле сканера: " + str(field) + " Тл")
        except (OSError, ValueError):
            lines.append("Метаданные сканирования: повреждены или не читаются")

    lines.extend(["", "РЕЗУЛЬТАТ / ЗАКЛЮЧЕНИЕ"])
    result_path = out / "pacni.csv"
    run_path = out / "run.json"
    if not finished or not structurally_valid or not provenance_ok:
        lines.append("Численный результат DunedinPACNI недоступен для подтвержденной интерпретации.")
        if not finished:
            lines.append("Что делать: запустить/завершить пункт 3 (FreeSurfer recon-all).")
        elif not structurally_valid:
            lines.append("Что делать: проверить/выполнить пункт 4 (структурный QC).")
        else:
            lines.append("Что делать: проверить выбранный T1 и согласованность метаданных.")
    elif not result_path.is_file() or not run_path.is_file():
        lines.append("DunedinPACNI еще не рассчитан; данных FreeSurfer недостаточно для числа.")
        lines.append("Что делать: выполнить пункт 5 (DunedinPACNI score).")
    else:
        try:
            run = json.loads(run_path.read_text(encoding="utf-8"))
            if not isinstance(run, dict) or run.get("study") != study.name:
                raise ValueError("run.json does not match the selected study")
            if run.get("result") != "pacni.csv":
                raise ValueError("run.json does not reference pacni.csv")
            if run.get("source_variant", "original") != variant:
                raise ValueError("Run input variant mismatch")
            if run.get("freesurfer_image") != FS_IMAGE:
                raise ValueError("FreeSurfer version differs from current configuration")
            with result_path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not reader.fieldnames or not {"ID", "pacni"}.issubset(reader.fieldnames):
                    raise ValueError("CSV must include ID and pacni columns")
                records = list(reader)
            if len(records) != 1 or records[0]["ID"] != subject_id(study, variant):
                raise ValueError("CSV participant ID or row count mismatch")
            value = float(records[0]["pacni"])
            if not math.isfinite(value):
                raise ValueError("Invalid non-finite pacni value")
            lines.append("DunedinPACNI: " + format(value, ".5f"))
            lines.append("Модель: " + (
                "без показателей GWR" if not run.get("gwr_available", False)
                else "с показателями GWR"
            ))
            lines.append(
                "Интерпретация: исследовательская оценка темпа биологического старения "
                "по структурной МРТ, а не «возраст мозга» в годах."
            )
            lines.append(
                "Более высокое значение соответствует более быстрому темпу "
                "по шкале модели; для индивидуального диагноза и оценки "
                "изменения во времени одной точки недостаточно."
            )
        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
            lines.append("Численный результат найден, но не прошел проверку: " +
                         str(error))
            lines.append("Результат нельзя интерпретировать; проверь пункт 5 и файлы расчета.")

    if variant == "synthsr":
        lines.extend([
            "",
            "ВНИМАНИЕ: СИНТЕТИЧЕСКИЙ РЕЗУЛЬТАТ",
            "SynthSR предсказывает промежуточную анатомию по обученной модели.",
            "Синтетические срезы не являются измерением и не годятся для "
            "радиологического заключения или исключения патологий.",
            "SynthSR может замещать патологические изменения нормальной тканью.",
            "Синтетический сигнал GWR исключен из модели PACNI.",
            "Результат не сопоставим напрямую с PACNI по оригинальной 3D T1.",
        ])
    lines.extend([
        "",
        "ОГРАНИЧЕНИЯ",
        "Автоматический QC проверяет наличие и структуру файлов, но не качество "
        "границ мозга, поверхностей и анатомической сегментации.",
        "Перед выводами требуется визуальная проверка FreeSurfer.",
        "DunedinPACNI не является радиологическим заключением или диагнозом.",
        "Модель разработана с FreeSurfer 6.0; текущая конфигурация использует "
        + FS_IMAGE + ".",
        "Сравнение разных лет требует сопоставимых сканеров/протоколов "
        "и одинаковой методики обработки.",
    ])
    return "\n".join(lines) + "\n"


def report_study(study, variant="original"):
    """Show the complete local report and keep a UTF-8 copy for later inspection."""
    summary = build_study_report(study, variant)
    output = RESULTS / subject_id(study, variant) / "report.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(summary, encoding="utf-8")
    print(summary)
    print("Отчет сохранен:", output)
    return output


def score(study, variant="original"):
    image = active_input(study, variant)
    provenance(study, image, variant)
    report = quality_gate(study, variant)
    check_docker()
    out = RESULTS / subject_id(study, variant)
    (out / "sublist.csv").write_text("ID\n" + subject_id(study, variant) + "\n", encoding="utf-8")
    sh(["docker", "build", "-f", str(ROOT / "docker" / "Dockerfile.pacni"),
        "-t", R_IMAGE, str(ROOT)])
    sh(["docker", "run", "--rm",
        "--mount", "type=bind,source=" + str(SUBJECTS.resolve()) +
        ",target=/subjects,readonly",
        "--mount", "type=bind,source=" + str(out.resolve()) + ",target=/results",
        R_IMAGE, "Rscript", "/opt/pacni/run_pacni.R",
        # SynthSR invents contrast. Never treat its GWR as a measured signal.
        "/subjects", "/results",
        "TRUE" if variant == "synthsr" or not report["gwr_available"] else "FALSE"])
    files = sorted(out.glob("*_df_pacni.csv"), key=lambda p: p.stat().st_mtime_ns)
    if not files:
        raise PipelineError("No PACNI CSV produced by R")
    shutil.copyfile(files[-1], out / "pacni.csv")
    (out / "run.json").write_text(json.dumps({
        "study": study.name, "freesurfer_image": FS_IMAGE,
        "source_variant": variant, "synthetic": variant == "synthsr",
        "gwr_available": report["gwr_available"] if variant == "original" else False,
        "result": "pacni.csv", "qc": "automatic checks only; visual inspection pending"
    }, indent=2), encoding="utf-8")
    print("PACNI result:", out / "pacni.csv")
    if variant == "synthsr":
        print("SYNTHETIC RESEARCH OUTPUT — anatomy and contrast are AI-generated; no GWR used.")
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
    parser.add_argument("command", choices=["list", "convert", "preflight", "recon", "qc", "score", "all", "compare", "report", "upscale", "all-synthsr", "report-synthsr", "upscale-status"])
    parser.add_argument("study", nargs="?")
    args = parser.parse_args()
    if args.command == "compare":
        compare_study_metadata()
        return
    if args.command == "upscale-status":
        print(json.dumps({study.name: bool(synth_index(study)) for study in studies()},
                         ensure_ascii=True))
        return
    if args.command == "list":
        if not studies():
            print("Place scans under MRI/<study>/")
        for study in studies():
            mark = " [UPSCALED via SynthSR]" if synth_index(study) else ""
            try:
                image = select_input(study)
                status = "[complete]" if completed(study) else "[not processed]"
                print(study.name, "->", image.relative_to(study), status + mark)
            except PipelineError as error:
                print(study.name, "[native T1 not available]", error, mark)
        return
    if args.study is None:
        parser.error("A study name is required")
    study = get_study(args.study)
    if args.command == "upscale":
        try:
            upscaler().upscale(study)
        except Exception as error:
            raise PipelineError(str(error)) from error
        return
    if args.command == "all-synthsr":
        reconstruct(study, "synthsr")
        score(study, "synthsr")
        return
    if args.command == "report-synthsr":
        report_study(study, "synthsr")
        return
    if args.command == "convert":
        subprocess.run([sys.executable, str(ROOT / "scripts" / "dicom_import.py"),
                        study.name], check=True)
        return
    if args.command == "report":
        report_study(study)
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
