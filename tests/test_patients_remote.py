"""Patient registry and selective-cloud behavioral regression tests; synthetic data only."""
from __future__ import annotations
import datetime
import gzip
import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import patient_registry as patients
import remote_mri as cloud


def dicom_bytes(series="1.2.3.44", patient="C0007", contrast="T1 MPRAGE"):
    """Build just a valid little-endian explicit VR test header."""
    data = bytearray(128) + bytearray(b"DICM")
    def tag(group, element, vr, value):
        val = value.encode("ascii")
        if len(val) % 2: val += b" "
        return struct.pack("<HH",group,element)+vr.encode("ascii")+struct.pack("<H",len(val))+val
    data += tag(0x0002, 0x0010, "UI", "1.2.840.10008.1.2.1")
    data += tag(0x0008, 0x0060, "CS", "MR")
    data += tag(0x0008, 0x103e, "LO", contrast)
    data += tag(0x0010, 0x0020, "LO", patient)
    data += tag(0x0020, 0x000e, "UI", series)
    return bytes(data).ljust(262144,b"\0")


class PatientsAndCloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_dir = self.root/"patients"
        self.legacy = self.root/"MRI"
        self.legacy.mkdir()
        self.patchers = [
            mock.patch.object(patients, "ROOT", self.root),
            mock.patch.object(patients, "DATA", self.db_dir),
            mock.patch.object(patients, "REGISTRY", self.db_dir/"registry.json"),
            mock.patch.object(patients, "LEGACY", self.legacy),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.tmp.cleanup()

    def test_names_and_no_path_traversal(self):
        for name in ["../foo", "AB/C", "A:BCD", "..", "a\\b", "name!"]:
            with self.subTest(name=name), self.assertRaises(patients.PatientError):
                patients.create_patient(name)
        pid = patients.create_patient("Test A7")
        self.assertTrue(patients.root_for(pid).is_dir())
        self.assertEqual(patients.resolve_patient("test a7"), pid)
        self.assertEqual(len(patients.list_patients()), 1)
        with self.assertRaises(patients.PatientError):
            patients.create_patient("test A7")
        self.assertFalse((self.root/"outside").exists())

    def test_existing_mri_assign_without_move_and_restart(self):
        old = self.legacy/"2018-05"
        old.mkdir()
        (old/"IM0001.dcm").write_bytes(b"unchanged")
        pid = patients.create_patient("A CODE")
        patients.assign_legacy(pid)
        self.assertEqual([(r.name,s.name) for r,s in patients.patient_studies(pid)],
                         [("MRI","2018-05")])
        self.assertEqual((old/"IM0001.dcm").read_bytes(),b"unchanged")
        self.assertEqual(patients.load_registry()["legacy_assignments"]["2018-05"],pid)
        with self.assertRaises(patients.PatientError):
            other=patients.create_patient("B CODE")
            patients.assign_legacy(other)

    def test_manual_merge_persisted_alias_but_no_raw_mri_move(self):
        a = patients.create_patient("A CODE")
        b = patients.create_patient("B CODE")
        folder = patients.root_for(b)/"2021"
        folder.mkdir()
        (folder/"T1.nii.gz").write_bytes(b"never move")
        patients.merge_patients(a,b)
        self.assertEqual(patients.resolve_patient("B CODE"),a)
        self.assertEqual(len(patients.list_patients()),1)
        self.assertEqual([s.name for _,s in patients.patient_studies(a)],["2021"])
        self.assertTrue(folder.exists())
        self.assertEqual((folder/"T1.nii.gz").read_bytes(),b"never move")
        self.assertEqual(len(patients.load_registry()["merges"]),1)

    def test_provider_allowlist_and_expiry(self):
        for bad in ["http://disk.yandex.ru/a","https://example.com/data",
                    "https://user:pw@disk.yandex.ru/d/x",
                    "https://disk.yandex.ru.evil.test/d/x",
                    "file:///etc/passwd"]:
            with self.subTest(bad=bad), self.assertRaises(cloud.CloudError):
                cloud.validate_share(bad)
        self.assertEqual(cloud.validate_share("https://disk.yandex.ru/d/test"),"yandex")
        self.assertEqual(cloud.validate_share("https://drive.google.com/drive/folders/abc"),"google")
        self.assertEqual(cloud.validate_share("https://www.dropbox.com/sh/abc"),"dropbox")
        with self.assertRaises(cloud.CloudError):
            cloud.validate_expiry("2000-01-01")
        with self.assertRaises(cloud.CloudError):
            cloud.validate_expiry("2050-01-01")

    def test_header_parse_and_non_t1_filter(self):
        h = cloud.parse_dicom(dicom_bytes())
        self.assertEqual(h["series_uid"],"1.2.3.44")
        self.assertEqual(h["modality"],"MR")
        self.assertEqual(h["patient_id"],"C0007")
        files = [{"name":f"IM{i:04d}","parent":"/SE1","path":f"/SE1/IM{i:04d}",
                  "size":10000,"provider":"yandex"} for i in range(30)]
        def fake_fetch(url,headers=None,post=None,max_bytes=None):
            return (206,{},dicom_bytes())
        with mock.patch.object(cloud,"media_request",return_value=("https://cloud-api.yandex.net/fake",{},None)):
            options = cloud.discover_series("https://disk.yandex.ru/d/a",files,fetch=fake_fetch)
        self.assertEqual(len(options),1)
        self.assertEqual(options[0]["count"],30)
        with mock.patch.object(cloud,"media_request",return_value=("https://cloud-api.yandex.net/fake",{},None)):
            cloud.validate_entire_series("https://disk.yandex.ru/d/a",options[0],fetch=fake_fetch)
        with mock.patch.object(cloud,"preview_dicom",return_value={"modality":"MR","series_uid":"1.2.3.44","description":"T2 FLAIR"}):
            self.assertEqual(cloud.discover_series("https://disk.yandex.ru/d/a",files),[])

    def test_refuse_mixed_uid_before_full_download(self):
        files = [{"name":f"IM{i:04d}","parent":"/SE1","path":f"/SE1/IM{i:04d}",
                  "size":10000,"provider":"yandex"} for i in range(16)]
        entry = {"uid":"1.2.3.44","files":files,"size":160000}
        count = {"n":0}
        def peek(share,file,fetch=None):
            count["n"]+=1
            return {"series_uid":"1.2.3.45" if count["n"]==5 else "1.2.3.44",
                    "modality":"MR","patient_id":"C0007"}
        with mock.patch.object(cloud,"preview_dicom",side_effect=peek), \
             mock.patch.object(cloud,"download_file") as download:
            with self.assertRaises(cloud.CloudError):
                cloud.validate_entire_series("https://disk.yandex.ru/d/a",entry)
            download.assert_not_called()

    def test_cloud_interactive_cancel_does_not_create_patient_mri(self):
        pid=patients.create_patient("CLIENT7")
        file={"name":"IM0001","parent":"/folder","path":"/folder/IM0001",
              "size":1000,"provider":"yandex"}
        option={"folder":"/folder","uid":"1.2.3","files":[file]*20,"size":20000,
                "description":"T1 MPRAGE","count":20}
        with mock.patch.object(cloud,"list_cloud",return_value=[file]), \
             mock.patch.object(cloud,"discover_series",return_value=[option]), \
             mock.patch.object(cloud,"validate_entire_series",return_value=True), \
             mock.patch.object(cloud,"download_file") as download:
            future=datetime.date.today().isoformat()
            iter_answers=iter(["1","2025-05-01","CANCEL"])
            with self.assertRaises(cloud.CloudError):
                cloud.interactive(pid,"https://disk.yandex.ru/d/test",future,
                                  input_fn=lambda prompt: next(iter_answers))
            download.assert_not_called()
        self.assertEqual(list(patients.root_for(pid).iterdir()),[])


if __name__ == "__main__":
    unittest.main()
