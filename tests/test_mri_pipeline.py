import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mri_pipeline.py"
spec = importlib.util.spec_from_file_location("mri_pipeline", SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [
            mock.patch.object(pipeline, "MRI", self.base / "MRI"),
            mock.patch.object(pipeline, "SUBJECTS", self.base / "work" / "freesurfer"),
            mock.patch.object(pipeline, "RESULTS", self.base / "results")
        ]
        for patch in self.patches:
            patch.start()
        pipeline.MRI.mkdir()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def make_study(self, name="2024-08-02"):
        folder = pipeline.MRI / name
        folder.mkdir()
        return folder

    def test_discovery_prefers_named_t1_over_flair(self):
        study = self.make_study()
        (study / "FLAIR.nii.gz").write_bytes(b"flair")
        (study / "T1.nii.gz").write_bytes(b"t1")
        self.assertEqual(pipeline.select_input(study).name, "T1.nii.gz")
        self.assertEqual(pipeline.get_study(study.name), study)

    def test_ambiguous_nifti_fails_closed(self):
        study = self.make_study()
        (study / "a.nii.gz").write_bytes(b"a")
        (study / "b.nii.gz").write_bytes(b"b")
        with self.assertRaises(pipeline.PipelineError):
            pipeline.select_input(study)

    def test_reject_study_path_traversal(self):
        self.make_study()
        with self.assertRaises(pipeline.PipelineError):
            pipeline.get_study("../outside")

    def test_dicom_requires_explicit_t1_folder(self):
        study = self.make_study()
        (study / "img0001.dcm").write_bytes(b"dcm")
        with self.assertRaises(pipeline.PipelineError):
            pipeline.select_input(study)
        dicom = study / "T1"
        dicom.mkdir()
        (dicom / "img0001.dcm").write_bytes(b"dcm")
        self.assertEqual(pipeline.select_input(study), dicom / "img0001.dcm")

    def test_provenance_blocks_changed_image(self):
        study = self.make_study()
        image = study / "T1.nii.gz"
        image.write_bytes(b"first")
        subject = pipeline.subject_dir(study)
        subject.mkdir(parents=True)
        (subject / "pacni-input.json").write_text(json.dumps({
            "signature": pipeline.signature(study, image),
            "fs_image": pipeline.FS_IMAGE
        }))
        pipeline.provenance(study, image)
        image.write_bytes(b"different")
        with self.assertRaises(pipeline.PipelineError):
            pipeline.provenance(study, image)

    def test_qc_only_checks_structure_not_visual_review(self):
        study = self.make_study()
        subject = pipeline.subject_dir(study)
        (subject / "stats").mkdir(parents=True)
        (subject / "scripts").mkdir()
        (subject / "scripts" / "recon-all.done").write_text("ok")
        for name in pipeline.STATS:
            (subject / "stats" / name).write_text(
                "# ColHeaders StructName Volume_mm3\n" + "region 1\n" * 40
            )
        report = pipeline.inspect(study)
        self.assertEqual(report["structural_qc"], "pass")
        self.assertEqual(report["visual_qc"], "not_performed")
        self.assertFalse(report["gwr_available"])

    def test_cross_year_comparability_is_explicitly_uncertain(self):
        one = self.make_study("2024")
        two = self.make_study("2026")
        result = pipeline.compare_study_metadata()
        self.assertEqual(result["status"], "insufficient_metadata")
        self.assertEqual(set(result["missing_metadata"]), {"2024", "2026"})
        metadata = {"scanner_model": "Scanner A", "field_strength_t": 3,
                    "sequence": "3D MPRAGE", "voxel_size_mm": [1, 1, 1]}
        for study in (one, two):
            (study / "scan.json").write_text(json.dumps(metadata))
        result = pipeline.compare_study_metadata()
        self.assertEqual(result["status"], "metadata_consistent_not_validated")
        metadata["field_strength_t"] = 1.5
        (two / "scan.json").write_text(json.dumps(metadata))
        result = pipeline.compare_study_metadata()
        self.assertEqual(result["status"], "review_required")
        self.assertTrue(result["differences"])

    def test_qc_blocks_incomplete_stats(self):
        study = self.make_study()
        with self.assertRaises(pipeline.PipelineError):
            pipeline.quality_gate(study)


if __name__ == "__main__":
    unittest.main()
