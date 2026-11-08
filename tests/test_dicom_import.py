"""Tests for the recursive DICOM importer using mock conversion, no patient data."""
import gzip
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dicom_import.py"
spec = importlib.util.spec_from_file_location("dicom_import", SCRIPT)
imp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imp)


def create_nifti(path, vox=(1.0, 1.0, 1.0), volumes=1):
    header = bytearray(352)
    struct.pack_into("<I", header, 0, 348)
    struct.pack_into("<8h", header, 40, 4 if volumes > 1 else 3,
                     176, 220, 184, volumes, 1, 1, 1)
    struct.pack_into("<8f", header, 76, 1.0, *vox, 1, 1, 1, 1)
    with gzip.open(path, "wb") as f:
        f.write(header)


class DicomImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            mock.patch.object(imp, "MRI", self.root / "MRI"),
            mock.patch.object(imp, "CACHE", self.root / "work" / "imports"),
            mock.patch.object(imp, "converter_path", return_value="dcm2niix"),
        ]
        for patcher in self.patches:
            patcher.start()
        imp.MRI.mkdir()
        self.study = imp.MRI / "2015.07 MRI Brain"
        self.study.mkdir()
        self.series = self.study / "SE000013"
        self.series.mkdir()
        (self.series / "IM0001.dcm").write_bytes(b"synthetic dicom placeholder")

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def runner(self, args):
        self.assertIn("-d", args)
        self.assertEqual(args[args.index("-d") + 1], "9")
        self.assertIn("-ba", args)
        folder = Path(args[args.index("-o") + 1])
        create_nifti(folder / "01_T1_MPRAGE.nii.gz")
        (folder / "01_T1_MPRAGE.json").write_text(json.dumps({
            "SeriesDescription": "3D T1 MPRAGE",
            "MRAcquisitionType": "3D",
            "ManufacturersModelName": "Example scanner",
            "MagneticFieldStrength": 3
        }), encoding="utf-8")
        create_nifti(folder / "02_FLAIR.nii.gz")
        (folder / "02_FLAIR.json").write_text(json.dumps({
            "SeriesDescription": "3D FLAIR", "MRAcquisitionType": "3D"
        }), encoding="utf-8")

    def test_recursive_series_discovery(self):
        names = [str(p.relative_to(self.study)) for p in imp.source_files(self.study)]
        self.assertEqual(names, ["SE000013/IM0001.dcm"])

    def test_filter_does_not_allow_flair(self):
        output = self.root / "outputs"
        output.mkdir()
        self.runner(["dcm2niix", "-d", "9", "-ba", "y", "-o", str(output)])
        images = imp.collect_candidates(output)
        self.assertEqual([x["status"] for x in images],
                         ["LIKELY_T1", "NOT_T1"])

    def test_import_publishes_only_confirmed_t1(self):
        inputs = iter(["1", "YES"])
        output = imp.convert(self.study, run_fn=self.runner,
                             input_fn=lambda prompt: next(inputs))
        self.assertEqual(output, self.study / "T1.nii.gz")
        self.assertTrue((self.study / "T1.json").is_file())
        self.assertTrue((self.study / "scan.json").is_file())
        self.assertEqual((self.series / "IM0001.dcm").read_bytes(),
                         b"synthetic dicom placeholder")
        record = json.loads((self.study / "pacni-import.json").read_text())
        self.assertEqual(record["status"], "selected_by_operator")
        self.assertEqual(imp.convert(self.study), output)

    def test_refuses_unconfirmed_or_non_t1_selection(self):
        for answer in ("2", "0", ""):
            with self.subTest(answer=answer):
                with self.assertRaises(imp.ImportErrorMRI):
                    imp.convert(self.study, run_fn=self.runner,
                                input_fn=lambda prompt: answer)
                self.assertFalse((self.study / "T1.nii.gz").exists())

    def test_does_not_allow_2d_or_low_resolution_t1(self):
        output = self.root / "geometry"
        output.mkdir()
        img = output / "T1.nii.gz"
        create_nifti(img, vox=(0.8, 0.8, 5.0))
        self.assertEqual(
            imp.classify(img, {"SeriesDescription": "T1", "MRAcquisitionType": "3D"})[0],
            "LOW_RESOLUTION"
        )
        create_nifti(img)
        self.assertEqual(
            imp.classify(img, {"SeriesDescription": "T1", "MRAcquisitionType": "2D"})[0],
            "NOT_3D"
        )

    def test_series_without_t1_is_not_relabelled(self):
        def only_flair(args):
            folder = Path(args[args.index("-o") + 1])
            create_nifti(folder / "FLAIR.nii.gz")
            (folder / "FLAIR.json").write_text(
                json.dumps({"SeriesDescription": "T2 FLAIR"}))
        with self.assertRaises(imp.ImportErrorMRI):
            imp.convert(self.study, run_fn=only_flair, input_fn=lambda _: "1")
        self.assertFalse((self.study / "T1.nii.gz").exists())
        self.assertTrue(
            (imp.CACHE / imp.study_id(self.study) / "series-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
