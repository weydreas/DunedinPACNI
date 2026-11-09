"""Read-only MRI report generation tests using synthetic results only."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mri_pipeline.py"
spec = importlib.util.spec_from_file_location("mri_report_pipeline", SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [
            mock.patch.object(pipeline, "MRI", self.base / "MRI"),
            mock.patch.object(pipeline, "SUBJECTS", self.base / "work" / "freesurfer"),
            mock.patch.object(pipeline, "PROVENANCE", self.base / "work" / "provenance"),
            mock.patch.object(pipeline, "RESULTS", self.base / "results"),
        ]
        for patcher in self.patches:
            patcher.start()
        pipeline.MRI.mkdir()
        self.study = pipeline.MRI / "2015.07 MRI"
        self.study.mkdir()
        (self.study / "T1.nii.gz").write_bytes(b"synthetic T1 volume")

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def finish_reconstruction(self):
        image = self.study / "T1.nii.gz"
        pipeline.record_provenance(self.study, image, pipeline.provenance(self.study, image))
        subject = pipeline.subject_dir(self.study)
        (subject / "scripts").mkdir(parents=True)
        (subject / "scripts" / "recon-all.done").write_text("done")
        (subject / "stats").mkdir()
        for name in pipeline.STATS:
            (subject / "stats" / name).write_text(
                "# ColHeaders StructName Volume_mm3\n" + "region 123\n" * 40
            )

    def complete_score(self):
        out = pipeline.RESULTS / pipeline.subject_id(self.study)
        out.mkdir(parents=True, exist_ok=True)
        (out / "pacni.csv").write_text(
            f'"ID","pacni"\n"{pipeline.subject_id(self.study)}",1.012345678\n',
            encoding="utf-8",
        )
        (out / "run.json").write_text(json.dumps({
            "study": self.study.name,
            "freesurfer_image": pipeline.FS_IMAGE,
            "gwr_available": False,
            "result": "pacni.csv",
        }), encoding="utf-8")
        return out

    def test_missing_reconstruction_is_reported_without_looking_up_docker(self):
        with mock.patch.object(pipeline, "check_docker") as docker:
            text = pipeline.build_study_report(self.study)
        docker.assert_not_called()
        self.assertIn("не запускался", text)
        self.assertIn("Численный результат DunedinPACNI недоступен", text)
        self.assertIn("пункт 3", text)
        self.assertNotIn("DunedinPACNI: 1.", text)
        with mock.patch("builtins.print"):
            output = pipeline.report_study(self.study)
        self.assertTrue(output.is_file())
        self.assertIn("2015.07 MRI", output.read_text(encoding="utf-8"))

    def test_qc_is_present_but_score_not_run(self):
        self.finish_reconstruction()
        text = pipeline.build_study_report(self.study)
        self.assertIn("Автоматический структурный QC: пройден", text)
        self.assertIn("DunedinPACNI еще не рассчитан", text)
        self.assertIn("пункт 5", text)

    def test_valid_output_prints_exact_numeric_score_with_caveats(self):
        self.finish_reconstruction()
        self.complete_score()
        text = pipeline.build_study_report(self.study)
        self.assertIn("DunedinPACNI: 1.01235", text)
        self.assertIn("не «возраст мозга»", text)
        self.assertIn("Визуальная оценка сегментации", text)
        self.assertIn("не является радиологическим заключением", text)

    def test_wrong_subject_id_is_rejected(self):
        self.finish_reconstruction()
        out = self.complete_score()
        (out / "pacni.csv").write_text('"ID","pacni"\n"wrong",999.0\n')
        text = pipeline.build_study_report(self.study)
        self.assertIn("не прошел проверку", text)
        self.assertNotIn("DunedinPACNI: 999", text)

    def test_nonfinite_score_is_rejected(self):
        self.finish_reconstruction()
        out = self.complete_score()
        (out / "pacni.csv").write_text(
            f'ID,pacni\n{pipeline.subject_id(self.study)},NaN\n'
        )
        text = pipeline.build_study_report(self.study)
        self.assertIn("не прошел проверку", text)
        self.assertNotIn("DunedinPACNI: nan", text)

    def test_wrong_run_metadata_is_rejected(self):
        self.finish_reconstruction()
        out = self.complete_score()
        (out / "run.json").write_text(json.dumps({
            "study": "different study", "freesurfer_image": pipeline.FS_IMAGE,
            "result": "pacni.csv",
        }))
        text = pipeline.build_study_report(self.study)
        self.assertIn("не прошел проверку", text)
        self.assertNotIn("DunedinPACNI: 1.01235", text)

    def test_score_file_without_complete_recon_cannot_be_reported(self):
        self.complete_score()
        text = pipeline.build_study_report(self.study)
        self.assertIn("Численный результат DunedinPACNI недоступен", text)
        self.assertNotIn("DunedinPACNI: 1.01235", text)

    def test_input_changed_blocks_stale_score(self):
        self.finish_reconstruction()
        self.complete_score()
        (self.study / "T1.nii.gz").write_bytes(b"modified input volume")
        text = pipeline.build_study_report(self.study)
        self.assertIn("Целостность входных данных: НЕ ПОДТВЕРЖДЕНА", text)
        self.assertNotIn("DunedinPACNI: 1.01235", text)


if __name__ == "__main__":
    unittest.main()
