"""Patient -> studies menu contract: no PHI, real files or Docker needed."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import patient_registry as registry
import patient_cli as cli


class PatientMRISelectionContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.legacy=self.root/"MRI"
        self.legacy.mkdir()
        self.patchers=[
            mock.patch.object(registry,"ROOT",self.root),
            mock.patch.object(registry,"DATA",self.root/"patients"),
            mock.patch.object(registry,"REGISTRY",self.root/"patients"/"registry.json"),
            mock.patch.object(registry,"LEGACY",self.legacy),
        ]
        for p in self.patchers: p.start()

    def tearDown(self):
        for p in reversed(self.patchers): p.stop()
        self.temp.cleanup()

    def render(self, patient):
        sink=io.StringIO()
        with contextlib.redirect_stdout(sink):
            cli.studies_menu_json(patient)
        result=json.loads(sink.getvalue())
        self.assertIsInstance(result,dict)
        self.assertEqual(result["count"],len(result["studies"]))
        self.assertEqual(result["patient_id"],patient)
        self.assertIsInstance(result["studies"],list)
        return result

    def test_zero_one_three_mri_round_trip_without_moving_originals(self):
        pid=registry.create_patient("TESTPERSON7")
        self.assertEqual(self.render(pid)["studies"],[])
        names=["2015.07 TEST MRI","2018.05 TEST MRI","2021.08 TEST MRI"]
        expected=[]
        for index,name in enumerate(names):
            study=self.legacy/name
            study.mkdir()
            scan=study/"sentinel.dcm"
            scan.write_bytes(b"not a clinical file" + bytes([index]))
            expected.append(scan.read_bytes())
            registry.assign_legacy(pid,name)
            menu=self.render(pid)
            self.assertEqual(menu["count"],index+1)
            self.assertEqual({x["name"] for x in menu["studies"]},
                             set(names[:index+1]))
            self.assertTrue(all(x["legacy"] for x in menu["studies"]))
            self.assertTrue(all(x["root"]==str(self.legacy) for x in menu["studies"]))
            self.assertTrue(all(x["patient_id"]==pid for x in menu["studies"]))
            self.assertTrue(all(x["storage_patient_id"]=="" for x in menu["studies"]))
        # Re-open local registry to model the next launcher process.
        self.assertEqual(registry.load_registry()["legacy_assignments"],
                         dict.fromkeys(names,pid))
        self.assertEqual(self.render(pid)["count"],3)
        for name,original in zip(names,expected):
            self.assertEqual((self.legacy/name/"sentinel.dcm").read_bytes(),original)

    def test_merged_patient_mri_remains_in_second_menu_with_storage_namespace(self):
        a=registry.create_patient("TESTALPHA")
        b=registry.create_patient("TESTBETA")
        study=registry.root_for(b)/"2026.08 MRI"
        study.mkdir()
        (study/"source.dcm").write_bytes(b"unmodified")
        registry.merge_patients(a,b)
        result=self.render(a)
        self.assertEqual(result["count"],1)
        self.assertEqual(result["studies"][0]["storage_patient_id"],b)
        self.assertEqual(result["studies"][0]["root"],str(study.parent))
        self.assertEqual((study/"source.dcm").read_bytes(),b"unmodified")

if __name__=="__main__":
    unittest.main()
