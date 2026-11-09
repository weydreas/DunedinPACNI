"""Synthetic/test-only SynthSR import, provenance, study identity, and safety tests."""
import gzip
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

sr = load("scripts/synthsr_upscale.py", "synthsr_upscale_test")
pipeline = load("scripts/mri_pipeline.py", "pacni_pipeline_synth_test")


def nifti(shape, voxel):
    header = bytearray(352)
    struct.pack_into("<I", header, 0, 348)
    struct.pack_into("<8h", header, 40, 3, *shape, 1, 1, 1, 1)
    struct.pack_into("<8f", header, 76, 1, *voxel, 1, 1, 1, 1)
    return gzip.compress(bytes(header) + b"\0" * 32)


class SynthSRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.study = self.root / "MRI" / "2018.05 МРТ"
        self.study.mkdir(parents=True)
        self.patches = [
            mock.patch.object(sr, "ROOT", self.root),
            mock.patch.object(sr, "MRI", self.root / "MRI"),
            mock.patch.object(sr, "IMPORTS", self.root / "work" / "imports"),
            mock.patch.object(sr, "UPSCALED", self.root / "work" / "upscaled"),
            mock.patch.object(pipeline, "ROOT", self.root),
            mock.patch.object(pipeline, "MRI", self.root / "MRI"),
            mock.patch.object(pipeline, "SUBJECTS", self.root / "work" / "freesurfer"),
            mock.patch.object(pipeline, "PROVENANCE", self.root / "work" / "provenance"),
            mock.patch.object(pipeline, "RESULTS", self.root / "results"),
            mock.patch.object(pipeline, "upscaler", lambda: sr),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def import_series(self, series):
        folder = sr.converted_dir(self.study)
        folder.mkdir(parents=True)
        manifest = {"study": self.study.name, "series": []}
        for i, (desc, shape, voxel) in enumerate(series):
            name = f"series_{i}.nii.gz"
            (folder / name).write_bytes(nifti(shape, voxel))
            manifest["series"].append({
                "file": name, "description": desc,
                "status": "NOT_3D", "nifti": {"shape": shape, "voxel_mm": voxel}
            })
        (folder.parent / "series-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )

    def generate(self, answers=("1", "UPSCALE")):
        responses = iter(answers)
        commands = []
        def fake_run(argv):
            commands.append(argv)
            if argv[:2] == ["docker", "run"]:
                out = sr.storage(self.study) / sr.file_hash(
                    sr.candidates(self.study)[0]["path"])[:16] / "T1_synthsr.nii.gz"
                out.write_bytes(nifti((200, 200, 160), (1, 1, 1)))
        sr.upscale(self.study, input_fn=lambda question: next(responses),
                   run_fn=fake_run)
        return commands

    def test_tiny_localizer_and_tof_not_upscaled(self):
        self.import_series([
            ("Cor T1 SE", (256, 256, 27), (.859, .859, 6.5)),
            ("Pituitary Sag T1", (512, 512, 9), (.313, .312, 3.3)),
            ("Ax 3DTOF SPGR", (512, 512, 120), (.43, .43, .7)),
            ("Ax T2 FLAIR", (448, 512, 24), (.449, .449, 6.5)),
        ])
        labels = [x["label"] for x in sr.candidates(self.study)]
        self.assertEqual(labels, ["Cor T1 SE", "Ax T2 FLAIR"])

    def test_one_study_with_one_synthetic_variant(self):
        self.import_series([("Cor T1 SE", (256, 256, 27), (.859, .859, 6.5))])
        original = sr.candidates(self.study)[0]["path"]
        before = original.read_bytes()
        commands = self.generate()
        self.assertEqual(original.read_bytes(), before)
        index = sr.read_index(self.study)
        self.assertIsNotNone(index)
        self.assertTrue(index["synthetic"])
        self.assertTrue(index["output_path"].is_file())
        self.assertIn("--network", commands[-1])
        self.assertIn("none", commands[-1])
        self.assertTrue(any("readonly" in token for token in commands[-1]))
        self.assertEqual(len(pipeline.studies()), 1)
        self.assertEqual(
            pipeline.subject_id(self.study, "synthsr"),
            pipeline.subject_id(self.study) + "_synthsr_" + index["source_sha256"][:8]
        )

    def test_no_confirmation_no_docker_no_new_file(self):
        self.import_series([("Cor T1 SE", (256, 256, 27), (.859, .859, 6.5))])
        with self.assertRaises(sr.UpscaleError):
            sr.upscale(self.study, input_fn=lambda q: "Q",
                       run_fn=lambda args: self.fail("No Docker should run"))
        self.assertFalse((sr.storage(self.study) / "index.json").exists())

    def test_changed_source_invalidates_marker_and_processing(self):
        self.import_series([("Cor T1 SE", (256, 256, 27), (.859, .859, 6.5))])
        self.generate()
        source = sr.candidates(self.study)[0]["path"]
        source.write_bytes(source.read_bytes() + b"changed")
        self.assertIsNone(sr.read_index(self.study))
        with self.assertRaises(pipeline.PipelineError):
            pipeline.active_input(self.study, "synthsr")

    def test_original_and_synthetic_paths_and_provenance_are_separate(self):
        self.import_series([("Cor T1 SE", (256, 256, 27), (.859, .859, 6.5))])
        self.generate()
        (self.study / "T1.nii.gz").write_bytes(nifti((176, 200, 200), (1, 1, 1)))
        native = pipeline.active_input(self.study)
        synthetic = pipeline.active_input(self.study, "synthsr")
        self.assertNotEqual(native, synthetic)
        self.assertNotEqual(pipeline.subject_dir(self.study),
                            pipeline.subject_dir(self.study, "synthsr"))
        metadata = pipeline.provenance(self.study, synthetic, "synthsr")
        pipeline.record_provenance(self.study, synthetic, metadata, "synthsr")
        self.assertTrue(pipeline.provenance_path(self.study, "synthsr").is_file())
        self.assertFalse(pipeline.provenance_path(self.study).exists())

    def test_original_input_required_for_native_processing_not_reused(self):
        self.import_series([("Cor T1 SE", (256, 256, 27), (.859, .859, 6.5))])
        self.generate()
        with self.assertRaises(pipeline.PipelineError):
            pipeline.active_input(self.study)
        self.assertTrue(pipeline.active_input(self.study, "synthsr").exists())

    def test_synthsr_report_has_clear_mandatory_warning(self):
        self.import_series([("Cor T1 SE", (256, 256, 27), (.859, .859, 6.5))])
        self.generate()
        text = pipeline.build_study_report(self.study, "synthsr")
        self.assertIn("СИНТЕТИЧЕСКИЙ", text)
        self.assertIn("патологические", text)
        self.assertIn("GWR исключен", text)

    def test_synthsr_scoring_forces_no_gwr_and_separate_result(self):
        self.import_series([("Cor T1 SE", (256, 256, 27), (.859, .859, 6.5))])
        self.generate()
        out = pipeline.RESULTS / pipeline.subject_id(self.study, "synthsr")
        calls = []

        def synthetic_qc(study, variant="original"):
            self.assertEqual(variant, "synthsr")
            out.mkdir(parents=True, exist_ok=True)
            return {"gwr_available": True, "structural_qc": "pass"}

        def fake_shell(args):
            calls.append(args)
            if "Rscript" in args:
                (out / "synthetic_df_pacni.csv").write_text(
                    "ID,pacni\n" + pipeline.subject_id(self.study, "synthsr") +
                    ",0.8\n", encoding="utf-8"
                )

        with mock.patch.object(pipeline, "quality_gate", side_effect=synthetic_qc), \
             mock.patch.object(pipeline, "check_docker"), \
             mock.patch.object(pipeline, "sh", side_effect=fake_shell):
            pipeline.score(self.study, "synthsr")
        run = json.loads((out / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["source_variant"], "synthsr")
        self.assertTrue(run["synthetic"])
        self.assertFalse(run["gwr_available"])
        rcmd = next(cmd for cmd in calls if "Rscript" in cmd)
        self.assertEqual(rcmd[-1], "TRUE")
        self.assertFalse((pipeline.RESULTS / pipeline.subject_id(self.study)).exists())

    def test_no_processing_when_manifest_is_foreign_study(self):
        self.import_series([("Cor T1 SE", (256, 256, 27), (.859, .859, 6.5))])
        mp = sr.converted_dir(self.study).parent / "series-manifest.json"
        data = json.loads(mp.read_text(encoding="utf-8"))
        data["study"] = "another"
        mp.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(sr.UpscaleError):
            sr.candidates(self.study)


if __name__ == "__main__":
    unittest.main()
