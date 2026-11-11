"""Full synthetic local workflow: patient -> cloud T1 -> import -> FreeSurfer/SynthSR.

Exercises production module contracts and storage paths without PHI, Docker,
real cloud access, or moving any original study.
"""
import contextlib
import datetime
import gzip
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import patient_registry as registry
import patient_cli as cli
import remote_mri as cloud

def module(file,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/"scripts"/file)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

pipeline=module("mri_pipeline.py","pipeline_integrated")
dicom=module("dicom_import.py","dicom_integrated")
synthsr=module("synthsr_upscale.py","synthsr_integrated")


def fake_dicom(uid="1.2.840.10009.1",ident="NONPATIENT"):
    result=bytearray(128)+bytearray(b"DICM")
    for group,element,vr,value in [
        (0x0002,0x0010,"UI","1.2.840.10008.1.2.1"),
        (0x0008,0x0060,"CS","MR"),
        (0x0008,0x103e,"LO","T1 MPRAGE"),
        (0x0010,0x0010,"PN","TEST PERSON"),
        (0x0010,0x0020,"LO",ident),
        (0x0020,0x000e,"UI",uid),
    ]:
        body=value.encode("ascii")
        if len(body)%2:body+=b" "
        result+=struct.pack("<HH",group,element)+vr.encode()+struct.pack("<H",len(body))+body
    return bytes(result).ljust(cloud.MAX_PREVIEW,b"\0")

def nifti():
    h=bytearray(352)
    struct.pack_into("<I",h,0,348)
    struct.pack_into("<8h",h,40,3,176,220,184,1,1,1,1)
    struct.pack_into("<8f",h,76,1,1,1,1,1,1,1,1)
    return gzip.compress(bytes(h)+b"\0"*32)


class IntegratedPatientWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/"MRI").mkdir()
        self.patches=[
            mock.patch.object(registry,"ROOT",self.root),
            mock.patch.object(registry,"DATA",self.root/"patients"),
            mock.patch.object(registry,"REGISTRY",self.root/"patients"/"registry.json"),
            mock.patch.object(registry,"LEGACY",self.root/"MRI"),
            mock.patch.object(dicom,"ROOT",self.root),
            mock.patch.object(dicom,"MRI",self.root/"MRI"),
            mock.patch.object(dicom,"CACHE",self.root/"work"/"imports"),
            mock.patch.object(dicom,"converter_path",return_value="dcm2niix"),
            mock.patch.object(pipeline,"ROOT",self.root),
            mock.patch.object(pipeline,"MRI",self.root/"MRI"),
            mock.patch.object(pipeline,"SUBJECTS",self.root/"work"/"freesurfer"),
            mock.patch.object(pipeline,"PROVENANCE",self.root/"work"/"provenance"),
            mock.patch.object(pipeline,"RESULTS",self.root/"results"),
            mock.patch.object(synthsr,"ROOT",self.root),
            mock.patch.object(synthsr,"MRI",self.root/"MRI"),
            mock.patch.object(synthsr,"IMPORTS",self.root/"work"/"imports"),
            mock.patch.object(synthsr,"UPSCALED",self.root/"work"/"upscaled")
        ]
        for p in self.patches:p.start()
        self.old_env={k:os.environ.get(k) for k in
            ["PACNI_PATIENT_ID","PACNI_MRI_ROOT","PACNI_VISIBLE_STUDIES"]}
        for key in self.old_env:os.environ.pop(key,None)

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        for key,val in self.old_env.items():
            if val is None:os.environ.pop(key,None)
            else:os.environ[key]=val
        self.tmp.cleanup()

    def cli(self,command,*argv):
        buf=io.StringIO()
        with mock.patch.object(sys,"argv",["patient_cli",command,*argv]), \
             contextlib.redirect_stdout(buf):
            cli.main()
        return buf.getvalue().strip()

    def run_test_workflow(self):
        # An existing clinical MRI must never move or be altered.
        legacy=self.root/"MRI"/"2015.07 MRI brain"
        legacy.mkdir()
        olddata=legacy/"T1.nii.gz"
        olddata.write_bytes(nifti())
        old_native_id=pipeline.subject_id(legacy)
        main=self.cli("create","TEST ALPHA")
        other=self.cli("create","TEST BETA")
        self.cli("attach-legacy",main,legacy.name)
        self.assertIn(legacy.name,self.cli("studies",main))
        self.assertEqual(pipeline.subject_id(legacy),old_native_id)

        raw=fake_dicom()
        entries=[{"name":f"IM{x:04d}","path":f"/T1/IM{x:04d}",
                  "parent":"/T1","size":len(raw),"provider":"yandex"}
                 for x in range(24)]
        option={"folder":"/T1","uid":"1.2.840.10009.1","files":entries,
                "size":24*len(raw),"count":24,"grade":"NATIVE_3D_T1_CANDIDATE",
                "description":"T1 MPRAGE"}
        def mock_download(share,file,dest,fetch=None):
            dest.write_bytes(raw)
            return len(raw)
        answers=iter(["1","2021.08 MRI brain","IMPORT"])
        with mock.patch.object(cloud,"list_cloud",return_value=entries), \
             mock.patch.object(cloud,"discover_series",return_value=[option]), \
             mock.patch.object(cloud,"validate_entire_series",return_value="NONPATIENT"), \
             mock.patch.object(cloud,"download_file",side_effect=mock_download):
            with contextlib.redirect_stdout(io.StringIO()):
                cloud.interactive(other,"https://disk.yandex.ru/d/SYNTHETIC",
                   datetime.date.today().isoformat(),input_fn=lambda prompt:next(answers))
        imported=registry.root_for(other)/"2021.08 MRI brain"
        self.assertEqual(len(list(imported.glob("*.dcm"))),24)
        self.assertNotIn("disk.yandex.ru",(imported/"cloud-import.json").read_text())

        self.cli("merge",main,other)
        persisted=registry.load_registry()
        self.assertEqual(registry.resolve_patient("TEST BETA",persisted),main)
        self.assertEqual(len(persisted["patients"]),1)
        self.assertEqual(len(registry.patient_studies(main)),2)
        self.assertTrue(olddata.exists())
        self.assertTrue(imported.exists())
        self.assertEqual(pipeline.subject_id(legacy),old_native_id)

        # Simulate exactly the environment mapping that the Windows launcher
        # applies for a selected patient's physical MRI root.
        os.environ["PACNI_PATIENT_ID"]=other
        os.environ["PACNI_MRI_ROOT"]=str(imported.parent)
        os.environ["PACNI_VISIBLE_STUDIES"]=json.dumps([imported.name])
        with mock.patch.object(pipeline,"MRI",imported.parent), \
             mock.patch.object(dicom,"MRI",imported.parent), \
             mock.patch.object(synthsr,"MRI",imported.parent):
            self.assertEqual(pipeline.studies(),[imported])
            with self.assertRaises(pipeline.PipelineError):
                pipeline.get_study(legacy.name)
            unique_id=pipeline.subject_id(imported)
            self.assertNotEqual(unique_id,old_native_id)
            self.assertEqual(unique_id,dicom.study_id(imported))
            self.assertEqual(unique_id,synthsr.study_id(imported))

            def fake_converter(args):
                out=Path(args[args.index("-o")+1])
                (out/"synthetic_mprage.nii.gz").write_bytes(nifti())
                (out/"synthetic_mprage.json").write_text(
                    json.dumps({"SeriesDescription":"3D T1 MPRAGE",
                        "MRAcquisitionType":"3D"}),encoding="utf-8")
            answers=iter(["1","YES"])
            with contextlib.redirect_stdout(io.StringIO()):
                out=dicom.convert(imported,run_fn=fake_converter,
                          input_fn=lambda prompt: next(answers))
            self.assertEqual(out,imported/"T1.nii.gz")
            self.assertTrue((imported/"pacni-import.json").exists())
            self.assertEqual(pipeline.active_input(imported),out)
            self.assertEqual(pipeline.select_input(imported),out)
            self.assertEqual(synthsr.candidates(imported)[0]["path"],out)
            with mock.patch.object(pipeline,"check_license"), \
                 mock.patch.object(pipeline,"check_docker"), \
                 mock.patch.object(pipeline,"sh") as runner:
                with self.assertRaisesRegex(pipeline.PipelineError,"recon-all.done"):
                    pipeline.reconstruct(imported)
            self.assertIn("-i",runner.call_args.args[0])
            self.assertTrue(pipeline.provenance_path(imported).is_file())
            self.assertFalse((self.root/"work"/"freesurfer"/old_native_id).exists())
            self.assertEqual(len(list((self.root/"work"/"imports"/unique_id/"converted").glob("*.nii.gz"))),1)

    def test_local_patient_header_registration_requires_complete_identity(self):
        study=self.root/"MRI"/"2018.05 MRI"
        study.mkdir()
        (study/"IM0001.dcm").write_bytes(fake_dicom(ident="PATIENT_A"))
        (study/"IM0002.dcm").write_bytes(fake_dicom(ident="PATIENT_B"))
        with mock.patch.dict(sys.modules,{"pydicom":None}):
            result=registry.register_discovered()
        self.assertEqual(result["registered"],[])
        self.assertEqual(registry.list_patients(),[])
        self.assertIn(study.name,result["unknown_studies"])

    def test_unreadable_local_header_blocks_automatic_registration(self):
        study=self.root/"MRI"/"2018.05 MRI"
        study.mkdir()
        (study/"IM0001.dcm").write_bytes(fake_dicom(ident="PATIENT_A"))
        (study/"IM0002.dcm").write_bytes(b"not a dicom")
        with mock.patch.dict(sys.modules,{"pydicom":None}):
            result=registry.register_discovered()
        self.assertEqual(result["registered"],[])
        self.assertIn(study.name,result["unknown_studies"])

    def test_merged_patient_comparability_across_roots(self):
        p1=self.cli("create","TEST ALPHA")
        p2=self.cli("create","TEST BETA")
        first=registry.root_for(p1)/"2021"
        second=registry.root_for(p2)/"2021"
        first.mkdir()
        second.mkdir()
        data={"scanner_model":"ScannerA","field_strength_t":3,
              "sequence":"MPRAGE","voxel_size_mm":[1,1,1]}
        first_meta=dict(data)
        second_meta=dict(data,scanner_model="ScannerB")
        (first/"scan.json").write_text(json.dumps(first_meta),encoding="utf-8")
        (second/"scan.json").write_text(json.dumps(second_meta),encoding="utf-8")
        self.cli("merge",p1,p2)
        scans=json.loads(self.cli("studies",p1))
        self.assertEqual(len(scans),2)
        self.assertNotEqual(scans[0]["storage_patient_id"],scans[1]["storage_patient_id"])
        self.assertTrue(all(x["name"]=="2021" for x in scans))
        result=json.loads(self.cli("compare",p1))
        self.assertEqual(len(result["studies"]),2)
        self.assertEqual(result["status"],"review_required")
        self.assertTrue(any(d["field"]=="scanner_model" for d in result["differences"]))
        self.assertTrue((self.root/"results"/"patients"/p1/"comparability.json").exists())

    def test_complete_local_cloud_identity_import_recon_chain(self):
        self.run_test_workflow()


if __name__=="__main__":
    unittest.main()
